from sqlalchemy import create_engine, MetaData, Table, select

engine = create_engine('sqlite:///C:/Users/AdityaAradhya/AppData/Local/Programs/Python/Python310/OpenAlgo/openalgo/db/openalgo.db')
metadata = MetaData()
metadata.reflect(bind=engine)

users_table = metadata.tables['users']
print("User table columns:", users_table.columns.keys())

# Display actual rows
with engine.connect() as conn:
    result = conn.execute(select(users_table)).fetchall()
    for row in result:
        print(dict(row._mapping))

