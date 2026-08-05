<?php
// access.php
require_once 'vendor/autoload.php'; // Cargar las dependencias de Composer 
// Configura tus cabeceras para responder en formato JSON
header('Content-Type: application/json');
// Reemplaza esto con tu Client ID real de Google
$CLIENT_ID = "TU_CLIENT_ID_DE_GOOGLE.apps.googleusercontent.com";
// Verificamos que la petición sea POST y tenga la acción correcta
if ($_SERVER['REQUEST_METHOD'] === 'POST' && isset($_POST['action']) && $_POST['action'] === 'google_login') {
    
    // Validamos que nos haya llegado el token
    if (!isset($_POST['token']) || empty($_POST['token'])) {
        echo json_encode(['success' => false, 'message' => 'Token no proporcionado.']);
        exit;
    }
    $idToken = $_POST['token'];
    try {
        // 1. Verificar el Token con la librería de Google
        $client = new Google_Client(['client_id' => $CLIENT_ID]);
        $payload = $client->verifyIdToken($idToken);
        if ($payload) {
            // El token es válido. Google nos retorna los datos del usuario de forma segura
            $googleId = $payload['sub']; // ID único de usuario en Google
            $email = $payload['email'];   // Email del usuario
            $name = $payload['name'];     // Nombre completo
            $picture = $payload['picture']; // URL de su foto de perfil
            // 2. Lógica de Negocio y Base de Datos
            // Aquí debes conectar a tu BD y buscar si el $email ya existe
            /*
            $usuario = buscarUsuarioPorEmail($email); // Tu función personalizada
            */
            
            // Simulación de flujo:
            $usuarioExiste = true; // Cambiar según el resultado de tu consulta a la BD
            if ($usuarioExiste) {
                // El usuario ya existe -> Iniciamos sesión
                session_start();
                $_SESSION['user'] = [
                    'email' => $email,
                    'name' => $name,
                    'logged_in' => true
                ];
                echo json_encode(['success' => true, 'message' => 'Login exitoso.']);
                exit;
            } else {
                // El usuario NO existe -> Registro Express
                /*
                $nuevoUsuario = registrarNuevoUsuario($email, $name, $googleId); // Tu función
                */
                
                // Una vez creado, también le iniciás la sesión
                session_start();
                $_SESSION['user'] = [
                    'email' => $email,
                    'name' => $name,
                    'logged_in' => true
                ];
                echo json_encode(['success' => true, 'message' => 'Usuario registrado e ingresado con éxito.']);
                exit;
            }
        } else {
            // Token inválido o alterado
            echo json_encode(['success' => false, 'message' => 'Token de Google inválido.']);
            exit;
        }
    } catch (Exception $e) {
        // Error en el proceso de verificación (ej. problemas de conexión o librería)
        echo json_encode(['success' => false, 'message' => 'Error al verificar la identidad: ' . $e->getMessage()]);
        exit;
    }
} else {
    // Petición inválida
    echo json_encode(['success' => false, 'message' => 'Solicitud no permitida.']);
    exit;
}

?>