from sqlalchemy import create_engine, MetaData, Table
from argon2 import PasswordHasher

# Setup
engine = create_engine('sqlite:///db/openalgo.db')
metadata = MetaData()
metadata.reflect(bind=engine)
users_table = metadata.tables['users']
ph = PasswordHasher()

# Set your new password
new_password = "Trading1!2025"  # <-- Change this as needed
hashed_pw = ph.hash(new_password)

# Update the password_hash column
with engine.connect() as conn:
    update = users_table.update().where(users_table.c.username == 'amarnath').values(password_hash=hashed_pw)
    conn.execute(update)
    print("✅ Password updated successfully!")
