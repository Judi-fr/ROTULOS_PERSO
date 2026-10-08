# PHP + Apache con las extensiones que pide Magento. La raíz web es pub/ (lo
# recomendado desde Magento 2.4).
FROM docker.io/library/php:8.3-apache

ADD https://github.com/mlocati/docker-php-extension-installer/releases/latest/download/install-php-extensions /usr/local/bin/
RUN chmod +x /usr/local/bin/install-php-extensions \
    && install-php-extensions bcmath gd intl pdo_mysql soap sockets xsl zip opcache @composer \
    && apt-get update && apt-get install -y --no-install-recommends unzip git && rm -rf /var/lib/apt/lists/*

ENV APACHE_DOCUMENT_ROOT=/var/www/html/pub
RUN sed -ri 's!/var/www/html!${APACHE_DOCUMENT_ROOT}!g' /etc/apache2/sites-available/*.conf \
    && sed -ri 's!AllowOverride None!AllowOverride All!g' /etc/apache2/apache2.conf \
    && a2enmod rewrite headers expires \
    # Codespaces termina el HTTPS y le pasa HTTP a Apache con X-Forwarded-Proto:
    # sin esto Magento se cree en HTTP, redirige en círculo y arma mal las URLs.
    && printf 'SetEnvIf X-Forwarded-Proto "https" HTTPS=on\n' > /etc/apache2/conf-enabled/forwarded-https.conf

COPY php.ini /usr/local/etc/php/conf.d/magento.ini
WORKDIR /var/www/html
