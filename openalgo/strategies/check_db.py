from sqlalchemy import create_engine, MetaData

# Connect to the correct database
engine = create_engine('sqlite:///C:/Users/AdityaAradhya/AppData/Local/Programs/Python/Python310/OpenAlgo/openalgo/db/openalgo.db')
metadata = MetaData()
metadata.reflect(bind=engine)


# Print all available table names
print("Available tables:", metadata.tables.keys())
