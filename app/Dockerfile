# Python 3.11をベースイメージとして使用
FROM python:3.11-slim

# 作業ディレクトリを設定
WORKDIR /app

# 必要なパッケージをインストール
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# アプリケーションファイルをコンテナにコピー
COPY main.py /app/
COPY mcp_config.json /app/

# 環境変数を設定
ENV AWS_DEFAULT_REGION=us-west-2

# コンテナのポート80を公開（ALB設定に合わせる）
EXPOSE 80

# Streamlitをポート80で実行
CMD ["streamlit", "run", "main.py", "--server.port=80", "--server.address=0.0.0.0"]