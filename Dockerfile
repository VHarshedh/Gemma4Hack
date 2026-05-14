FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for audio, video, and llama-cpp
RUN apt-get update && apt-get install -y \
    build-essential \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libasound2 \
    portaudio19-dev \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Initialize mock DB if it doesn't exist
RUN python setup_db.py

EXPOSE 8091

CMD ["python", "command_node.py", "--host", "0.0.0.0", "--port", "8091"]
