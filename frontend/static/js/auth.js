// Shared auth utilities — loaded on every page via base.html
//
// Tokens are HttpOnly cookies set by the server — JS never touches them.
// We only store a non-sensitive user profile object in sessionStorage so
// the UI can show the right nav links and avatar initials without an extra
// network call on every page.

(async function () {
    let user = _loadUser();

    if (!user) {
        // No cached profile — ask the server (cookie is sent automatically)
        try {
            const res = await fetch('/auth/me', { credentials: 'include' });
            if (res.ok) {
                user = await res.json();
                _saveUser(user);
            }
        } catch (_) { /* network error — leave nav links hidden */ }
    }

    if (user) {
        const navChat  = document.getElementById('nav-chat');
        const navAdmin = document.getElementById('nav-admin');
        const btnLogout = document.getElementById('btn-logout');
        const navLogin  = document.getElementById('nav-login');

        if (navChat)   navChat.style.display   = 'inline-flex';
        if (btnLogout) btnLogout.style.display  = 'inline-flex';
        if (navLogin)  navLogin.style.display   = 'none';
        if (navAdmin && user.is_admin) navAdmin.style.display = 'inline-flex';
    }
})();

function _loadUser() {
    try { return JSON.parse(sessionStorage.getItem('user') || 'null'); }
    catch (_) { return null; }
}

function _saveUser(user) {
    try { sessionStorage.setItem('user', JSON.stringify(user)); }
    catch (_) {}
}

async function logout() {
    // Ask the server to clear the HttpOnly cookies
    await fetch('/auth/logout', { method: 'POST', credentials: 'include' });
    sessionStorage.clear();
    window.location.href = '/';
}
