FROM nginx:1.29-alpine

COPY rp-showcase-gui/nginx.conf /etc/nginx/conf.d/default.conf
COPY rp-showcase-gui/index.html /usr/share/nginx/html/index.html
COPY rp-showcase-gui/styles.css /usr/share/nginx/html/styles.css
COPY rp-showcase-gui/pt-design.css /usr/share/nginx/html/pt-design.css
COPY rp-showcase-gui/assets /usr/share/nginx/html/assets
COPY rp-showcase-gui/structured-content.js /usr/share/nginx/html/structured-content.js
COPY rp-showcase-gui/message-time.js /usr/share/nginx/html/message-time.js
COPY rp-showcase-gui/app.js /usr/share/nginx/html/app.js
COPY ui-shared/training-artifacts.js /usr/share/nginx/html/training-artifacts.js
COPY ui-shared/training-artifacts.css /usr/share/nginx/html/training-artifacts.css

EXPOSE 80
