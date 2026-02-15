# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies for GUI and Inputs
# - python3-tk: For Tkinter
# - scrot, python3-xlib: For pyautogui/pynput dependencies (Linux specific)
# - libgl1, libglib2.0-0: For OpenCV (if used in server later)
RUN apt-get update && apt-get install -y \
    python3-tk \
    scrot \
    python3-xlib \
    x11-utils \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose ports
EXPOSE 9999
EXPOSE 9998

# Run server.py when the container launches
# -u forces stdout and stderr to be unbuffered.
CMD ["python", "-u", "server.py"]
