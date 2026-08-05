function handleCredentialResponse(response) {
    // 'response.credential' es un token JWT cifrado enviado por Google
    
    // Aquí usas tu lógica AJAX actual para enviarlo a tu backend
    $.ajax({
        url: 'access.php', // Tu archivo de login actual
        type: 'POST',
        data: { 
            action: 'google_login',
            token: response.credential 
        },
        success: function(res) {
            // Aquí manejas la redirección (ej. a menuVC.php) si el backend da el visto bueno
            if(res.success) {
                window.location.href = 'menuVC.php';
            } else {
                alert('Error al iniciar sesión con Google');
            }
        },
        error: function() {
            alert('error ajax');
        }
    });
}