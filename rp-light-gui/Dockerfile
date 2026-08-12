FROM nginx:1.27-alpine

COPY rp-light-gui/nginx.conf /etc/nginx/conf.d/default.conf
COPY rp-light-gui/index.html /usr/share/nginx/html/index.html
COPY rp-light-gui/styles.css /usr/share/nginx/html/styles.css
COPY rp-light-gui/structured-content.js /usr/share/nginx/html/structured-content.js
COPY rp-light-gui/message-time.js /usr/share/nginx/html/message-time.js
COPY rp-light-gui/app.js /usr/share/nginx/html/app.js
COPY rp-light-gui/trace.html /usr/share/nginx/html/trace.html
COPY rp-light-gui/trace.css /usr/share/nginx/html/trace.css
COPY rp-light-gui/trace.js /usr/share/nginx/html/trace.js
COPY ui-shared/training-artifacts.js /usr/share/nginx/html/training-artifacts.js
COPY ui-shared/training-artifacts.css /usr/share/nginx/html/training-artifacts.css

EXPOSE 80
