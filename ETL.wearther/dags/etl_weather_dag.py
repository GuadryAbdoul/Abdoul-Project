from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime
import requests
import psycopg2
import os
import logging


def fetch_weather_data(city: str, api_key: str) -> dict:
    base_url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={api_key}&units=metric"
    response = requests.get(base_url)

    if response.status_code == 200:
        data = response.json()
        return {
            "city": city,
            "temperature": data["main"]["temp"],
            "humidity": data["main"]["humidity"],
            "description": data["weather"][0]["description"],
            "date": str(datetime.utcnow().date())
        }
    else:
        raise Exception(f"Failed to fetch data for {city}: {response.text}")

# Extract task: get weather for all cities
def extract_weather(**context):
    api_key = os.getenv("OPENWEATHER_API_KEY")
    cities_env = os.getenv("CITIES", "")
    
    # Parse la variable d'environnement en liste (split sur la virgule)
    if cities_env:
        city_names = [city.strip() for city in cities_env.split(",") if city.strip()]
    else:
        # fallback si la variable CITIES n'est pas définie
        city_names = [
            "London", "Birmingham", "Manchester", "Glasgow", "Liverpool",
            "Leeds", "Sheffield", "Bristol", "Newcastle", "Nottingham"
        ]

    results = []
    for city in city_names:
        try:
            weather = fetch_weather_data(city, api_key)
            logging.info(f"Fetched weather for {city}: {weather}")
            results.append(weather)
        except Exception as e:
            logging.error(e)

    context['ti'].xcom_push(key='raw_weather', value=results)

# Transform task: could clean or enrich data
def transform_weather(**context):
    raw_data = context['ti'].xcom_pull(key='raw_weather', task_ids='extract_weather')
    transformed = []
    for record in raw_data:
        record['description'] = record['description'].capitalize()  # Simple transform
        transformed.append(record)
    context['ti'].xcom_push(key='clean_weather', value=transformed)

# Load task: insert into Postgres
def load_weather(**context):
    weather_data = context['ti'].xcom_pull(key='clean_weather', task_ids='transform_weather')
    conn = psycopg2.connect(
        host="postgres",
        database=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD")
    )
    cur = conn.cursor()
    for weather in weather_data:
        try:
            cur.execute("""
                INSERT INTO weather (city, temperature, humidity, weather_description, date)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                weather["city"],
                weather["temperature"],
                weather["humidity"],
                weather["description"],
                weather["date"]
            ))
            logging.info(f"Loaded: {weather}")
        except Exception as e:
            logging.error(f"Error loading weather for {weather['city']}: {e}")
    conn.commit()
    cur.close()
    conn.close()

# Define DAG
default_args = {
    "start_date": datetime(2024, 1, 1),
}

with DAG(
    dag_id="weather_etl_separated",
    schedule_interval="@daily",
    default_args=default_args,
    catchup=False
) as dag:

    t1 = PythonOperator(
        task_id="extract_weather",
        python_callable=extract_weather,
        provide_context=True
    )

    t2 = PythonOperator(
        task_id="transform_weather",
        python_callable=transform_weather,
        provide_context=True
    )

    t3 = PythonOperator(
        task_id="load_weather",
        python_callable=load_weather,
        provide_context=True
    )

    t1 >> t2 >> t3
