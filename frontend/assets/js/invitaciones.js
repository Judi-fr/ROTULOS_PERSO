// Invitaciones (invitaciones.html, /team/my-invitations/): los comercios que
// invitaron al usuario. Aceptar SUMA ese comercio a los suyos y la pestaña
// pasa a trabajar en él; el backend lo rechaza (y lo explica) si el usuario
// no verificó su mail. La lista la dibuja renderMyInvitations (utils.js).

function bootstrap() {
  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
    return;
  }
  const invitations = renderMyInvitations(document.getElementById("invitationList"), {
    onAccepted(data) {
      if (window.AppTopbar && data.user) window.AppTopbar.render(data.user);
      showMessage(`Ya sos parte de «${data.team_name}». Volvé al panel para ver lo que podés hacer.`, "success");
      invitations.load();
    },
  });
  invitations.load();
}

bootstrap();
