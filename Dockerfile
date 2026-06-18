FROM python:3.12-slim

# Impede que o Python escreva arquivos .pyc
ENV PYTHONDONTWRITEBYTECODE=1
# Garante que as saídas do stdout/stderr sejam entregues direto no terminal
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instala dependências do sistema necessárias para compilação do psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Instala as dependências do Python
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copia os arquivos do projeto para o contêiner
COPY . /app/

EXPOSE 8000

# Execução padrão do django dev server
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
