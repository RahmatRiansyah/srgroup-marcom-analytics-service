from fastapi import FastAPI

app = FastAPI(title="SRGroup Marcom Analytics Service")

@app.get("/")
def health_check():
    return {"status": "ok"}