FROM node:20-slim AS css-builder

WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm ci --omit=dev
COPY tailwind.config.js ./
COPY static/ static/
RUN npx tailwindcss -i static/css/tailwind-input.css -o static/css/tailwind.css --minify

FROM python:3.13-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY server.py processing.py ./
COPY --from=css-builder /build/static/ static/
COPY maps/ maps/

EXPOSE 8080

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
