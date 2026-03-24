# Use an official, lightweight Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Expose port 5000 so we can access the app from our browser
EXPOSE 5000

# Run the Flask app, binding to 0.0.0.0 so it is accessible outside the container
CMD ["flask", "run", "--host=0.0.0.0"]