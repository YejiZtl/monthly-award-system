"""Browser-held submission credentials; authorization is always enforced by the API."""
from hashlib import sha256
import re

from fastapi import Request
from fastapi.responses import JSONResponse

from app.security import AuthenticationError, verify_reviewer_token


def credential_hash(value):
    return sha256(value.encode()).hexdigest() if value and re.fullmatch(r'[A-Za-z0-9_-]{32,128}', value) else None


def install_submission_access(app, get_repository, get_settings):
    @app.middleware('http')
    async def submission_access(request: Request, call_next):
        repo, config = get_repository(), get_settings()
        path = request.url.path
        raw_key = request.headers.get('X-Submitter-Key') or request.cookies.get('monthly_award_submitter')
        token = request.headers.get('X-Reviewer-Token')
        reviewer = False
        try:
            # A cookie grants embedded, read-only material access only. API role
            # operations still require the reviewer header through their dependency.
            material_page = bool(re.match(r'^/api/v1/(documents/|batches/[^/]+/evidence$)', path))
            candidate = token or (request.cookies.get('monthly_award_material_reader') if material_page and request.method == 'GET' else None)
            if candidate or (not raw_key and not config.reviewer_token and config.app_env == 'test'):
                verify_reviewer_token(candidate)
                reviewer = True
        except AuthenticationError:
            pass
        owner = credential_hash(raw_key)
        request.state.submission_owner = owner
        request.state.is_reviewer = reviewer
        if request.method != 'OPTIONS' and not reviewer and path.startswith('/api/v1/'):
            batch_id = None
            match = re.match(r'^/api/v1/batches/([^/]+)(?:/|$)', path)
            if match:
                batch_id = match[1]
            match = re.match(r'^/api/v1/documents/([^/]+)(?:/|$)', path)
            if match:
                with repo.connection() as connection:
                    row = connection.execute('SELECT batch_id FROM source_documents WHERE id=?', (match[1],)).fetchone()
                if not row:
                    return JSONResponse({'detail':'原件不存在'}, status_code=404)
                batch_id = row['batch_id']
            match = re.match(r'^/api/v1/jobs/([^/]+)$', path)
            if match:
                job = repo.get_extraction_job(match[1])
                if not job:
                    return JSONResponse({'detail':'识别任务不存在'}, status_code=404)
                batch_id = job['batch_id']
            if batch_id and (not owner or not repo.owns_batch(batch_id, owner)):
                return JSONResponse({'detail':'批次不存在或无访问权限'}, status_code=404)
        response = await call_next(request)
        if owner and 'X-Submitter-Key' in request.headers:
            response.set_cookie('monthly_award_submitter', raw_key, httponly=True, samesite='lax',
                                secure=request.url.scheme == 'https', path='/api/v1', max_age=365*86400)
        if reviewer and token:
            response.set_cookie('monthly_award_material_reader', token, httponly=True, samesite='strict',
                                secure=request.url.scheme == 'https', path='/api/v1', max_age=8*3600)
        return response
