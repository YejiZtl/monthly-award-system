FROM nginx:1.27-alpine

COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/preview /usr/share/nginx/html/preview
COPY frontend/app/globals.css /usr/share/nginx/html/app/globals.css

EXPOSE 80
