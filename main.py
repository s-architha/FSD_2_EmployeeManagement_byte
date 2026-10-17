import os
import datetime as dt
import bcrypt
import jwt
from fastapi import FastAPI, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import create_engine, String, Float, Boolean
from sqlalchemy.orm import (DeclarativeBase, Mapped, mapped_column,
                            sessionmaker, Session)

SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-placeholder-key-change-me-in-production")
ALGORITHM = "HS256"

engine = create_engine("sqlite:///employees.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    role: Mapped[str] = mapped_column(String)
    department: Mapped[str] = mapped_column(String)
    salary: Mapped[float] = mapped_column(Float)

Base.metadata.create_all(engine)

def hash_pw(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()

# ---- Seed data (runs only when tables are empty) ----
with SessionLocal() as db:
    if not db.query(User).first():
        db.add(User(email="admin@example.com", password_hash=hash_pw("admin12345"), is_admin=True))
    if not db.query(Employee).first():
        db.add_all([
            Employee(name="Asha Nair", email="asha@example.com", role="Developer", department="Engineering", salary=65000),
            Employee(name="Ravi Kumar", email="ravi@example.com", role="Designer", department="Design", salary=55000),
            Employee(name="Meera Iyer", email="meera@example.com", role="HR Manager", department="HR", salary=60000),
        ])
    db.commit()

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class EmployeeIn(BaseModel):
    name: str = Field(min_length=2)
    email: EmailStr
    role: str = Field(min_length=2)
    department: str = Field(min_length=2)
    salary: float = Field(gt=0)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

bearer = HTTPBearer()

def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = db.get(User, int(payload["sub"]))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user

def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user

def emp_dict(e: Employee) -> dict:
    return {"id": e.id, "name": e.name, "email": e.email,
            "role": e.role, "department": e.department, "salary": e.salary}

app = FastAPI(title="Employee Management System")

@app.get("/", include_in_schema=False)
def home():
    return FileResponse("index.html")

@app.post("/login")
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(email=data.email).first()
    if not user or not bcrypt.checkpw(data.password.encode(), user.password_hash.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
    token = jwt.encode({"sub": str(user.id), "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer", "is_admin": user.is_admin}

@app.get("/employees")
def list_employees(db: Session = Depends(get_db)):
    return [emp_dict(e) for e in db.query(Employee).order_by(Employee.id).all()]

@app.get("/employees/{emp_id}")
def get_employee(emp_id: int, db: Session = Depends(get_db)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    return emp_dict(e)

@app.post("/employees", status_code=201)
def create_employee(data: EmployeeIn, db: Session = Depends(get_db),
                    _: User = Depends(require_admin)):
    if db.query(Employee).filter_by(email=data.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already exists")
    e = Employee(**data.model_dump())
    db.add(e)
    db.commit()
    db.refresh(e)
    return emp_dict(e)

@app.put("/employees/{emp_id}")
def update_employee(emp_id: int, data: EmployeeIn, db: Session = Depends(get_db),
                    _: User = Depends(require_admin)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    clash = db.query(Employee).filter(Employee.email == data.email, Employee.id != emp_id).first()
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already exists")
    for k, v in data.model_dump().items():
        setattr(e, k, v)
    db.commit()
    return emp_dict(e)

@app.delete("/employees/{emp_id}", status_code=204)
def delete_employee(emp_id: int, db: Session = Depends(get_db),
                    _: User = Depends(require_admin)):
    e = db.get(Employee, emp_id)
    if not e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Employee not found")
    db.delete(e)
    db.commit()
    return Response(status_code=204)