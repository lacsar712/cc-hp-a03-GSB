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
    pot_name: str = Field(min_length=1, max_length=40)
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


app = FastAPI(title="饮片炮制记录台")


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
        conn.execute(
            """CREATE TABLE IF NOT EXISTS pots (
                id serial PRIMARY KEY,
                name text NOT NULL UNIQUE,
                cert_no text NOT NULL,
                status text NOT NULL DEFAULT '有效',
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cert_events (
                id serial PRIMARY KEY,
                pot_name text NOT NULL,
                cert_no text,
                action text NOT NULL,
                detail text NOT NULL,
                actor text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute("ALTER TABLE batches ADD COLUMN IF NOT EXISTS pot_id integer REFERENCES pots(id)")
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


def log_event(conn, pot_name: str, cert_no: str | None, action: str, detail: str, actor: str):
    conn.execute(
        """INSERT INTO cert_events (pot_name, cert_no, action, detail, actor, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (pot_name, cert_no, action, detail, actor, datetime.now(timezone.utc)),
    )


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
            """SELECT b.id, b.herb, b.doc, b.verdict, b.reason, b.created_by, p.name AS pot_name
               FROM batches b LEFT JOIN pots p ON p.id = b.pot_id
               ORDER BY b.id DESC"""
        ).fetchall()
    return rows


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    with connect() as conn:
        pot = conn.execute("SELECT id, name, status FROM pots WHERE name = %s", (body.pot_name.strip(),)).fetchone()
        if pot is None:
            raise HTTPException(status_code=400, detail="所选锅具未挂校准证书，禁止开炒")
        if pot["status"] != "有效":
            raise HTTPException(status_code=400, detail="所选锅具的校准证书已作废，禁止开炒")
        row = conn.execute(
            """INSERT INTO batches (herb, pot_id, doc, verdict, reason, created_by, created_at)
               VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id, herb, pot_id, doc, verdict, reason, created_by""",
            (body.herb.strip(), pot["id"], json.dumps(doc, ensure_ascii=False), verdict, reason, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        row["pot_name"] = pot["name"]
        conn.commit()
    return row


@app.get("/api/pots")
def list_pots(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, cert_no, status, created_by, created_at FROM pots ORDER BY id"
        ).fetchall()
    return rows


@app.post("/api/pots", status_code=201)
def register_pot(body: PotIn, user: dict = Depends(require_writer)):
    name = body.name.strip()
    cert_no = body.cert_no.strip()
    with connect() as conn:
        pot = conn.execute("SELECT id, status FROM pots WHERE name = %s", (name,)).fetchone()
        now = datetime.now(timezone.utc)
        if pot and pot["status"] == "有效":
            raise HTTPException(status_code=409, detail="该锅已挂有效校准证书，如需换证请先作废")
        if pot:
            row = conn.execute(
                """UPDATE pots SET cert_no = %s, status = '有效', created_by = %s, created_at = %s
                   WHERE id = %s RETURNING id, name, cert_no, status, created_by, created_at""",
                (cert_no, user["username"], now, pot["id"]),
            ).fetchone()
        else:
            row = conn.execute(
                """INSERT INTO pots (name, cert_no, status, created_by, created_at)
                   VALUES (%s, %s, '有效', %s, %s)
                   RETURNING id, name, cert_no, status, created_by, created_at""",
                (name, cert_no, user["username"], now),
            ).fetchone()
        log_event(conn, name, cert_no, "登记", f"锅「{name}」挂校准证书 {cert_no}", user["username"])
        conn.commit()
    return row


@app.post("/api/pots/{pot_id}/void")
def void_pot(pot_id: int, user: dict = Depends(require_writer)):
    with connect() as conn:
        pot = conn.execute("SELECT id, name, cert_no, status FROM pots WHERE id = %s", (pot_id,)).fetchone()
        if pot is None:
            raise HTTPException(status_code=404, detail="锅具不存在")
        if pot["status"] == "作废":
            raise HTTPException(status_code=409, detail="该锅证书已作废")
        row = conn.execute(
            "UPDATE pots SET status = '作废' WHERE id = %s RETURNING id, name, cert_no, status, created_by, created_at",
            (pot_id,),
        ).fetchone()
        log_event(conn, pot["name"], pot["cert_no"], "作废", f"锅「{pot['name']}」校准证书 {pot['cert_no']} 作废", user["username"])
        conn.commit()
    return row


@app.patch("/api/pots/{pot_id}")
def rename_pot(pot_id: int, body: PotRenameIn, user: dict = Depends(require_writer)):
    new_name = body.name.strip()
    with connect() as conn:
        pot = conn.execute("SELECT id, name, cert_no, status FROM pots WHERE id = %s", (pot_id,)).fetchone()
        if pot is None:
            raise HTTPException(status_code=404, detail="锅具不存在")
        clash = conn.execute("SELECT id FROM pots WHERE name = %s AND id <> %s", (new_name, pot_id)).fetchone()
        if clash:
            raise HTTPException(status_code=409, detail="锅名已被占用")
        row = conn.execute(
            """UPDATE pots SET name = %s WHERE id = %s
               RETURNING id, name, cert_no, status, created_by, created_at""",
            (new_name, pot_id),
        ).fetchone()
        if new_name != pot["name"]:
            log_event(conn, new_name, pot["cert_no"], "改名", f"锅「{pot['name']}」改名为「{new_name}」，证书号 {pot['cert_no']} 不变", user["username"])
        conn.commit()
    return row


@app.get("/api/cert-events")
def list_cert_events(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, pot_name, cert_no, action, detail, actor, created_at FROM cert_events ORDER BY id DESC"
        ).fetchall()
    return rows
