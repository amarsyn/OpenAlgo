#update OpenAlgo API Key
from sqlalchemy import create_engine, MetaData

engine = create_engine('sqlite:///db/openalgo.db')
metadata = MetaData()
metadata.reflect(bind=engine)

users_table = metadata.tables['users']
new_apikey = "c78382d46b357e2d6031e1c664a2803e"  # Paste your new key here

with engine.connect() as conn:
    update = users_table.update().where(users_table.c.username == 'amarnath').values(apikey=new_apikey)
    conn.execute(update)
    print("✅ API Key updated in DB")
