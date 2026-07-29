// Asegúrate de que la librería de Google esté cargada en tu HTML
window.onload = function () {
    // 1. Inicializamos el cliente de Google detrás de escena
    google.accounts.id.initialize({
        client_id: "TU_CLIENT_ID_DE_GOOGLE.apps.googleusercontent.com",
        callback: handleCredentialResponse // La función que recibirá el token de Google
    });

    // 2. Escuchamos el clic en tu botón personalizado para abrir el cartel de Google
    document.getElementById("btnGoogleCustom").addEventListener("click", function(e) {
        e.preventDefault(); // Evita que el '#' recargue la página o mueva el scroll
        google.accounts.id.prompt(); // Muestra el cartel flotante nativo de Google "One Tap" o login
    });
}

function handleCredentialResponse(response) {
    // response.credential contiene el token JWT súper seguro que generó Google
    const googleToken = response.credential;

    // Enviamos el token a tu backend mediante AJAX (Fetch)
    fetch('access.php', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
            'action': 'google_login',
            'token': googleToken
        })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            // Si el login en el backend fue exitoso, redirigimos al panel/dashboard
            window.location.href = 'dashboard.php';
        } else {
            alert('Error en la autenticación: ' + data.message);
        }
    })
    .catch(error => console.error('Error en la petición AJAX:', error));
}