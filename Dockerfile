FROM python:3.12-alpine AS build
WORKDIR /app
COPY src ./src
COPY data ./data
COPY scripts ./scripts
RUN python scripts/build.py

FROM nginx:stable-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/public /usr/share/nginx/html
EXPOSE 80
