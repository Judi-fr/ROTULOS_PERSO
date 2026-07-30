const users = [
  {name:"John Smith", email:"john.smith@example.com", role:"Admin", status:"active", created:"May 12, 2024", seed:"john"},
  {name:"Sarah Johnson", email:"sarah.j@example.com", role:"Editor", status:"active", created:"May 10, 2024", seed:"sarah"},
  {name:"Michael Brown", email:"michael.b@example.com", role:"Author", status:"active", created:"May 8, 2024", seed:"michael"},
  {name:"Emily Davis", email:"emily.d@example.com", role:"Editor", status:"inactive", created:"May 5, 2024", seed:"emily"},
  {name:"David Wilson", email:"david.w@example.com", role:"Author", status:"active", created:"May 3, 2024", seed:"david"},
  {name:"Emma Thompson", email:"emma.t@example.com", role:"Subscriber", status:"inactive", created:"Apr 28, 2024", seed:"emma"},
  {name:"James Anderson", email:"james.a@example.com", role:"Author", status:"locked", created:"Apr 25, 2024", seed:"james"},
  {name:"Olivia Martinez", email:"olivia.m@example.com", role:"Subscriber", status:"active", created:"Apr 20, 2024", seed:"olivia"},
];
 
const statusLabel = { active:"Active", inactive:"Inactive", locked:"Locked" };
const roleClass = { Admin:"admin", Editor:"editor", Author:"author", Subscriber:"subscriber" };
 
const tbody = document.getElementById('usersBody');
users.forEach((u, i) => {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input type="checkbox"></td>
    <td>
      <div class="user-cell">
        <img class="user-avatar" src="https://api.dicebear.com/7.x/avataaars/svg?seed=${u.seed}" alt="">
        <span class="user-name">${u.name}</span>
      </div>
    </td>
    <td class="cell-muted">${u.email}</td>
    <td><span class="badge ${roleClass[u.role]}">${u.role}</span></td>
    <td><span class="status ${u.status}"><span class="dot"></span>${statusLabel[u.status]}</span></td>
    <td class="cell-muted">${u.created}</td>
    <td class="actions-col">
      <div class="row-actions">
        <button class="more-btn" data-idx="${i}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>
        </button>
        <div class="dropdown" id="dd-${i}" style="display:none;">
          <a href="#"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>View</a>
          <a href="#"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>Edit</a>
          <a href="#"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>Reset Password</a>
          <div class="sep"></div>
          <a href="#" class="danger"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>Lock User</a>
          <a href="#" class="danger"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>Delete</a>
        </div>
      </div>
    </td>
  `;
  tbody.appendChild(tr);
});
 
// dropdown toggle
document.querySelectorAll('.more-btn').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const idx = btn.dataset.idx;
    document.querySelectorAll('.dropdown').forEach(dd => {
      if (dd.id !== `dd-${idx}`) dd.style.display = 'none';
    });
    const dd = document.getElementById(`dd-${idx}`);
    dd.style.display = dd.style.display === 'block' ? 'none' : 'block';
  });
});
document.addEventListener('click', () => {
  document.querySelectorAll('.dropdown').forEach(dd => dd.style.display = 'none');
});
 
// create panel toggle
const panel = document.getElementById('createPanel');
document.getElementById('openCreatePanel').addEventListener('click', () => panel.style.display = 'block');
document.getElementById('closeCreatePanel').addEventListener('click', () => panel.style.display = 'none');
document.getElementById('cancelCreate').addEventListener('click', () => panel.style.display = 'none');