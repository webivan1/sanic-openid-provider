import uuid
import logging

import sanic.request
import sanic.response
import jwcrypto.jwk

from sanic_oicp.utils import get_scheme
from sanic_oicp.validation import *
from sanic_oicp.clients import Client


logger = logging.getLogger('oicp')


async def well_known_config_handler(request: sanic.request.Request) -> sanic.response.BaseHTTPResponse:
    scheme = get_scheme(request)

    response = {
        'issuer': '{0}://{1}'.format(scheme, request.host),
        'authorization_endpoint': request.app.url_for('authorize_handler', _scheme=scheme, _external=True, _server=request.host),
        'token_endpoint': request.app.url_for('token_handler', _scheme=scheme, _external=True, _server=request.host),
        'userinfo_endpoint': request.app.url_for('userinfo_handler', _scheme=scheme, _external=True, _server=request.host),
        'jwks_uri': request.app.url_for('jwk_handler', _scheme=scheme, _external=True, _server=request.host),
        'registration_endpoint': request.app.url_for('client_register_handler', _scheme=scheme, _external=True, _server=request.host),
        'login_hint': 'N/A',
        # 'end_session_endpoint'
        # 'introspection_endpoint'

        'request_parameter_supported': False,
        'response_types_supported': ['code', 'id_token', 'id_token token', 'code token', 'code id_token', 'code id_token token'],
        'id_token_signing_alg_values_supported': ['HS256', 'RS256'],

        'subject_types_supported': ['public', 'pairwise'],  # or pairwise
        'token_endpoint_auth_methods_supported': [
            'client_secret_post',
            'client_secret_basic',
            'private_key_jwt',
            'client_secret_jwt'
        ],  # Or client_secret_jwt

        'claims_supported': ['name', 'family_name', 'given_name', 'middle_name', 'nickname', 'preferred_username', 'profile', 'picture', 'website', 'gender', 'birthdate', 'zoneinfo', 'locale', 'updated_at', 'email', 'email_verified', 'address', 'phone_number', 'phone_number_verified'],
        'grant_types_supported': ['authorization_code', 'implicit', 'refresh_token', 'password', 'client_credentials']
    }
    return sanic.response.json(response, headers={'Access-Control-Allow-Origin': '*'})


async def well_known_finger_handler(request: sanic.request.Request) -> sanic.response.BaseHTTPResponse:
    scheme = get_scheme(request)

    resource = request.args.get('resource')
    rel = request.args.get('rel')

    logger.info('finger for resource: {0} rel: {1}'.format(resource, rel))

    finger_url = request.app.url_for('well_known_finger_handler', _scheme=scheme, _external=True, _server=request.host)

    if resource == finger_url and rel == 'http://openid.net/specs/connect/1.0/issuer':
        return sanic.response.json({
            "subject": resource,
            "links": [
                {
                    "rel": rel,
                    "href": '{0}://{1}'.format(scheme, request.host)
                }
            ]
        })
    elif resource.startswith('acct:') and rel == 'http://openid.net/specs/connect/1.0/issuer':
        return sanic.response.json({
            "subject": resource,
            "links": [
                {
                    "rel": rel,
                    "href": '{0}://{1}'.format(scheme, request.host)
                }
            ]
        })

    return sanic.response.HTTPResponse(status=500)


async def jwk_handler(request: sanic.request.Request) -> sanic.response.BaseHTTPResponse:
    # TODO return own jwks

    keys = []

    # async for client in request.app.config['oicp_client'].all():
    #     for jwk in client.jwk:
    #         keys.append(jwk.export(False))

    return sanic.response.json({'keys': keys})


async def userinfo_handler(request: sanic.request.Request) -> sanic.response.BaseHTTPResponse:
    try:
        params = await validate_userinfo_params(request)
        token = params['token']

        if token.get('specific_claims', {}) is None:
            specific_claims = {}
        else:
            specific_claims = token['specific_claims']

        specific_claims = specific_claims.get('userinfo', {}).keys()
        claims = await request.app.config['oicp_user'].get_claims_for_user_by_scope(token['user'], token['scope'], specific_claims)

        result = {
            'sub': token['user']
        }
        result.update(claims)

        return sanic.response.json(result, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'})

    except TokenError as error:

        return sanic.response.json(error.create_dict(), status=400)


async def client_register_handler(request: sanic.request.Request) -> sanic.response.BaseHTTPResponse:
    scheme = get_scheme(request)

    if 'client_id' in request.args:
        # Client Read, check auth header
        try:
            token = request.headers['Authorization'].split('Bearer ')[-1]
            client = await request.app.config['oicp_client'].get_client_by_access_token(token)
        except Exception as err:
            return sanic.response.text(body='', status=403, headers={'WWW-Authenticate': 'Bearer error="invalid_token"'})

        result = {
            'client_id': client.id,
            'client_secret': client.secret,
            'client_secret_expires_at': client.expires_at,
            'subject_type': client.type,

            'application_type': client.application_type,
            'response_types': client.response_types,
            'redirect_uris': client.callback_urls,
            'grant_types': client.grant_types,
            'contacts': client.contacts,
            'jwks_uri': client.jwks_url,
            'post_logout_redirect_uris': client.post_logout_redirect_urls,
            'request_uris': client.request_urls,

            # 'registration_client_uri': request.app.url_for('client_register_handler', _scheme=scheme, _external=True, _server=request.host, client_id=client_id),
            # 'registration_access_token': client.access_token,
        }
        if client.sector_identifier_uri:
            result['sector_identifier_uri'] = client.sector_identifier_uri
        if client.jwt_algo:
            result['id_token_signed_response_alg'] = client.jwt_algo

        status = 201

    else:
        client_id = uuid.uuid4().hex[:10].upper()
        client_name = request.json.get('client_name', client_id)
        client_secret = uuid.uuid4().hex
        client_secret_expires_at = 1577858400  # 1st jan 2020

        application_type = request.json.get('application_type')
        response_types = request.json.get('response_types')
        redirect_uris = request.json.get('redirect_uris', [])
        grant_types = request.json.get('grant_types')
        contacts = request.json.get('contacts')
        jwks_uri = request.json.get('jwks_uri')
        jwks = request.json.get('jwks')
        post_logout_redirect_uris = request.json.get('post_logout_redirect_uris')
        request_uris = request.json.get('request_uris')
        prompt = request.json.get('prompt', frozenset(['none', 'login', 'consent']))
        sector_identifier_uri = request.json.get('sector_identifier_uri')
        subject_type = request.json.get('subject_type', 'public')

        require_consent = request.json.get('require_consent') is True
        reuse_consent = request.json.get('reuse_consent') is True
        id_token_signed_response_alg = request.json.get('id_token_signed_response_alg')

        for url in redirect_uris:
            if '#' in url:
                # NO BAD, shouldnt have fragments in url
                result = {'error': 'invalid_redirect_uri', 'error_description': 'Bad redirect uri {0}'.format(url)}
                return sanic.response.json(result, status=400)

        success, data = await request.app.config['oicp_client'].add_client(
            id_=client_id,
            name=client_name,
            type_=subject_type,
            secret=client_secret,
            callback_urls=redirect_uris,
            require_consent=require_consent,
            reuse_consent=reuse_consent,
            response_types=response_types,
            application_type=application_type,
            contacts=contacts,
            expires_at=client_secret_expires_at,
            grant_types=grant_types,
            jwks_url=jwks_uri,
            jwt_algo=id_token_signed_response_alg,
            prompts=prompt,
            post_logout_redirect_urls=post_logout_redirect_uris,
            request_urls=request_uris,
            sector_identifier_uri=sector_identifier_uri
        )

        if success:
            client: Client = data
            await client.load_jwks(jwk_dict=jwks)

            result = {
                'client_id': client_id,
                'client_secret': client_secret,
                'client_secret_expires_at': client_secret_expires_at,
                'subject_type': 'confidential',

                'application_type': application_type,
                'response_types': response_types,
                'redirect_uris': redirect_uris,
                'grant_types': grant_types,
                'contacts': contacts,
                'jwks_uri': jwks_uri,
                'post_logout_redirect_uris': post_logout_redirect_uris,
                'request_uris': request_uris,

                'registration_client_uri': request.app.url_for('client_register_handler', _scheme=scheme, _external=True, _server=request.host, client_id=client_id),
                'registration_access_token': client.access_token,
                # 'token_endpoint_auth_method': 'client_secret_basic'
            }
            if sector_identifier_uri:
                result['sector_identifier_uri'] = sector_identifier_uri
            if id_token_signed_response_alg:
                result['id_token_signed_response_alg'] = id_token_signed_response_alg

            status = 201
        else:
            result = {
                'error': 'invalid_client_metadata',
                'error_description': data
            }
            status = 500

    return sanic.response.json(result, headers={'Cache-Control': 'no-store', 'Pragma': 'no-cache'}, status=status)











