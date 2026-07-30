FROM nginx:1.29-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY styles.css /usr/share/nginx/html/styles.css
COPY pt-design.css /usr/share/nginx/html/pt-design.css
COPY assets /usr/share/nginx/html/assets
COPY structured-content.js /usr/share/nginx/html/structured-content.js
COPY message-time.js /usr/share/nginx/html/message-time.js
COPY app.js /usr/share/nginx/html/app.js

EXPOSE 80
