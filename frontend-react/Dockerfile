# Build stage
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build \
    && mkdir -p dist/ops-navi \
    && sed 's|<base href="/"|<base href="/ops-navi/"|' dist/index.html > dist/ops-navi/index.html

# Serve stage
FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
# 템플릿 — 기동 때 nginx 이미지가 envsubst로 conf.d/default.conf를 만든다(정의된 환경변수만 바꿔 $remote_addr 등은 그대로)
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV REAL_IP_FROM=127.0.0.1/32 REAL_IP_FROM_2=127.0.0.1/32
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
