from sqlalchemy import text

from tourism_service import models  # noqa: F401
from tourism_service.database import Base, SessionLocal, engine
from tourism_service.seed import seed_demo_data


def reset_data() -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_demo_data(db)


if __name__ == "__main__":
    reset_data()
    print("Tourism service data reset completed.")
