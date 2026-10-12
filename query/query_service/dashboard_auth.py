"""Signed, HttpOnly dashboard sessions and rate-limited sign in."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

COOKIE = "house_session"


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)
    remember: bool = False


class DashboardSessions:
    def __init__(self, credentials):
        self.credentials = credentials
        self.key = hashlib.sha256(
            (credentials.username + "\0" + credentials.password).encode()
        ).digest()
        self.revoked = {}
        self.attempts = defaultdict(deque)

    def issue(self, seconds):
        payload = base64.urlsafe_b64encode(
            json.dumps(
                {"expires": int(time.time()) + seconds, "nonce": secrets.token_hex(16)},
                separators=(",", ":"),
            ).encode()
        ).decode()
        return (
            payload
            + "."
            + hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
        )

    def valid(self, token):
        if not token:
            return False
        try:
            payload, signature = token.split(".")
            if not hmac.compare_digest(
                signature,
                hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest(),
            ):
                return False
            body = json.loads(base64.urlsafe_b64decode(payload))
            return body["expires"] > time.time() and body["nonce"] not in self.revoked
        except (ValueError, KeyError, TypeError):
            return False

    @staticmethod
    def same_origin(request):
        origin = request.headers.get("origin")
        if request.headers.get("sec-fetch-site") == "cross-site" or (
            origin and urlsplit(origin).netloc != request.headers.get("host")
        ):
            raise HTTPException(403, "Cross-origin request rejected")

    def install(self, app):
        @app.post("/auth/login")
        def login(body: Login, request: Request):
            self.same_origin(request)
            address = request.client.host if request.client else "unknown"
            attempts = self.attempts[address]
            now = time.time()
            while attempts and attempts[0] < now - 60:
                attempts.popleft()
            if len(attempts) >= 10:
                raise HTTPException(429, "Too many attempts. Try again in one minute.")
            attempts.append(now)
            if not (
                secrets.compare_digest(
                    body.username.encode(), self.credentials.username.encode()
                )
                and secrets.compare_digest(
                    body.password.encode(), self.credentials.password.encode()
                )
            ):
                raise HTTPException(401, "Incorrect username or password")
            attempts.clear()
            seconds = 14 * 86400 if body.remember else 12 * 3600
            response = JSONResponse({"authenticated": True})
            response.set_cookie(
                COOKIE,
                self.issue(seconds),
                max_age=seconds if body.remember else None,
                httponly=True,
                secure=request.headers.get("x-forwarded-proto", request.url.scheme)
                == "https",
                samesite="strict",
                path="/",
            )
            response.headers["Cache-Control"] = "no-store"
            return response

        @app.get("/auth/session")
        def session(request: Request):
            return {"authenticated": self.valid(request.cookies.get(COOKIE))}

        @app.get("/auth/check")
        def check(request: Request):
            if not self.valid(request.cookies.get(COOKIE)):
                raise HTTPException(401, "Sign in required")
            return {"authenticated": True}

        @app.post("/auth/logout")
        def logout(request: Request):
            self.same_origin(request)
            token = request.cookies.get(COOKIE)
            if self.valid(token):
                body = json.loads(base64.urlsafe_b64decode(token.split(".")[0]))
                self.revoked = {
                    k: v for k, v in self.revoked.items() if v > time.time()
                }
                self.revoked[body["nonce"]] = body["expires"]
            response = JSONResponse({"authenticated": False})
            response.delete_cookie(COOKIE, path="/")
            return response
