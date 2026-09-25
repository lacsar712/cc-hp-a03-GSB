import json
import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from rules import judge

SECRET = os.environ.get("JWT_SECRET", "herb-process-dev-secret")
DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54393/herb")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "processor": {"role": "writer", "password_hash": pwd.hash("herb123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}

CERT_VALID = "有效"
CERT_VOID = "作废"


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


class LoginIn(BaseModel):
    username: str
    password: str


class StepIn(BaseModel):
    name: str
    temp_c: float
    minutes: float


class BatchIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    pot_id: int
    steps: list[StepIn]


class PotIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    cert_no: str = Field(min_length=1, max_length=80)


class PotRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅炮制员可写入记录")
    return user


def require_cert_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅炮制员可登记或作废证书")
    return user


app = FastAPI(title="饮片炮制记录台")


def log_cert_event(conn, pot: dict, action: str, detail: str, actor: str):
    conn.execute(
        """INSERT INTO cert_events (pot_id, pot_name, cert_no, action, detail, actor, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (pot["id"], pot["name"], pot["cert_no"], action, detail, actor, datetime.now(timezone.utc)),
    )


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS batches (
                id serial PRIMARY KEY,
                herb text NOT NULL,
                doc jsonb NOT NULL,
                verdict text NOT NULL,
                reason text NOT NULL,
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute("ALTER TABLE batches ADD COLUMN IF NOT EXISTS pot_id integer")
        conn.execute("ALTER TABLE batches ADD COLUMN IF NOT EXISTS pot_name text")
        conn.execute("ALTER TABLE batches ADD COLUMN IF NOT EXISTS cert_no text")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pots (
                id serial PRIMARY KEY,
                name text NOT NULL UNIQUE,
                cert_no text NOT NULL,
                cert_status text NOT NULL DEFAULT '有效',
                created_by text NOT NULL,
                created_at timestamptz NOT NULL,
                updated_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cert_events (
                id serial PRIMARY KEY,
                pot_id integer NOT NULL REFERENCES pots(id),
                pot_name text NOT NULL,
                cert_no text NOT NULL,
                action text NOT NULL,
                detail text NOT NULL,
                actor text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        if count == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("甘草", {"steps": [{"name": "清炒", "temp_c": 120, "minutes": 12}]}),
                ("黄芩", {"steps": [{"name": "清炒", "temp_c": 40, "minutes": 12}]}),
            ]
            for herb, doc in samples:
                verdict, reason = judge(doc)
                conn.execute(
                    """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at)
                       VALUES (%s, %s::jsonb, %s, %s, %s, %s)""",
                    (herb, json.dumps(doc, ensure_ascii=False), verdict, reason, "processor", now),
                )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "herb-process-record"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/batches")
def list_batches(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, herb, doc, verdict, reason, created_by, pot_id, pot_name, cert_no
               FROM batches ORDER BY id DESC"""
        ).fetchall()
    return rows


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    with connect() as conn:
        pot = conn.execute("SELECT id, name, cert_no, cert_status FROM pots WHERE id = %s", (body.pot_id,)).fetchone()
        if pot is None:
            raise HTTPException(status_code=400, detail="所选锅未挂校准证书，禁止开炒")
        if pot["cert_status"] != CERT_VALID:
            raise HTTPException(status_code=400, detail=f"锅「{pot['name']}」的证书已作废，禁止开炒")
        row = conn.execute(
            """INSERT INTO batches (herb, doc, verdict, reason, created_by, created_at, pot_id, pot_name, cert_no)
               VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, herb, doc, verdict, reason, created_by, pot_id, pot_name, cert_no""",
            (
                body.herb.strip(),
                json.dumps(doc, ensure_ascii=False),
                verdict,
                reason,
                user["username"],
                datetime.now(timezone.utc),
                pot["id"],
                pot["name"],
                pot["cert_no"],
            ),
        ).fetchone()
        conn.commit()
    return row


@app.get("/api/pots")
def list_pots(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, name, cert_no, cert_status, created_by, created_at, updated_at
               FROM pots ORDER BY id"""
        ).fetchall()
    return rows


@app.post("/api/pots", status_code=201)
def register_pot(body: PotIn, user: dict = Depends(require_cert_writer)):
    name = body.name.strip()
    cert_no = body.cert_no.strip()
    if not name or not cert_no:
        raise HTTPException(status_code=400, detail="锅名与证书号不能为空")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        existing = conn.execute("SELECT * FROM pots WHERE name = %s", (name,)).fetchone()
        if existing and existing["cert_status"] == CERT_VALID:
            raise HTTPException(status_code=409, detail=f"锅「{name}」已挂有效证书，不能重复登记")
        if existing:
            row = conn.execute(
                """UPDATE pots SET cert_no = %s, cert_status = %s, updated_at = %s
                   WHERE id = %s
                   RETURNING id, name, cert_no, cert_status, created_by, created_at, updated_at""",
                (cert_no, CERT_VALID, now, existing["id"]),
            ).fetchone()
            log_cert_event(conn, row, "登记", f"原证书作废后重新登记证书 {cert_no}", user["username"])
        else:
            row = conn.execute(
                """INSERT INTO pots (name, cert_no, cert_status, created_by, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id, name, cert_no, cert_status, created_by, created_at, updated_at""",
                (name, cert_no, CERT_VALID, user["username"], now, now),
            ).fetchone()
            log_cert_event(conn, row, "登记", f"登记证书 {cert_no}", user["username"])
        conn.commit()
    return row


@app.patch("/api/pots/{pot_id}")
def rename_pot(pot_id: int, body: PotRenameIn, user: dict = Depends(require_cert_writer)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="锅名不能为空")
    with connect() as conn:
        pot = conn.execute("SELECT * FROM pots WHERE id = %s", (pot_id,)).fetchone()
        if pot is None:
            raise HTTPException(status_code=404, detail="锅不存在")
        clash = conn.execute("SELECT id FROM pots WHERE name = %s AND id <> %s", (name, pot_id)).fetchone()
        if clash:
            raise HTTPException(status_code=409, detail=f"锅名「{name}」已被占用")
        row = conn.execute(
            """UPDATE pots SET name = %s, updated_at = %s
               WHERE id = %s
               RETURNING id, name, cert_no, cert_status, created_by, created_at, updated_at""",
            (name, datetime.now(timezone.utc), pot_id),
        ).fetchone()
        if pot["name"] != name:
            log_cert_event(conn, row, "改名", f"锅名由「{pot['name']}」改为「{name}」，证书号不变", user["username"])
        conn.commit()
    return row


@app.post("/api/pots/{pot_id}/void")
def void_pot(pot_id: int, user: dict = Depends(require_cert_writer)):
    with connect() as conn:
        pot = conn.execute("SELECT * FROM pots WHERE id = %s", (pot_id,)).fetchone()
        if pot is None:
            raise HTTPException(status_code=404, detail="锅不存在")
        if pot["cert_status"] == CERT_VOID:
            raise HTTPException(status_code=409, detail=f"锅「{pot['name']}」的证书已作废，不能重复作废")
        row = conn.execute(
            """UPDATE pots SET cert_status = %s, updated_at = %s
               WHERE id = %s
               RETURNING id, name, cert_no, cert_status, created_by, created_at, updated_at""",
            (CERT_VOID, datetime.now(timezone.utc), pot_id),
        ).fetchone()
        log_cert_event(conn, row, "作废", f"证书 {pot['cert_no']} 作废，禁止开炒", user["username"])
        conn.commit()
    return row


@app.get("/api/cert-events")
def list_cert_events(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, pot_id, pot_name, cert_no, action, detail, actor, created_at
               FROM cert_events ORDER BY id DESC"""
        ).fetchall()
    return rows
