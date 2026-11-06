# Pruebas de navegador

Abren la app en Chromium y hacen clic de verdad. Levantan la API con una base de datos
propia (no tocan la de desarrollo) y los mails salen por consola.

```
cd frontend/e2e
npm ci
npx playwright install chromium   # o E2E_CHROME=/ruta/a/chrome para usar uno ya instalado
npm test
```

Usan los puertos 8100 y 8101, así que corren aunque tengas la app levantada. Cada prueba cubre algo que se rompió alguna vez
o que no se puede ver desde la API; no repiten lo que ya prueban los tests del backend.

Si algo falla: `npx playwright show-report` y el registro de la API en `backend.log`.
