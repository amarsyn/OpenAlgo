from sqlalchemy import create_engine, MetaData

engine = create_engine('sqlite:///C:/Users/AdityaAradhya/AppData/Local/Programs/Python/Python310/OpenAlgo/openalgo/db/openalgo.db')
metadata = MetaData()
metadata.reflect(bind=engine)

api_keys_table = metadata.tables['api_keys']
with engine.connect() as conn:
    result = conn.execute(api_keys_table.select())
    for row in result:
        print(dict(row._mapping))
