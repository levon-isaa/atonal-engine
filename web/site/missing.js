// On an address with no studio, point the button at the main site (the part after the first dot).
(() => { const a = document.getElementById('home'); const host = location.host.split('.').slice(1).join('.'); if (a && host) { a.href = `${location.protocol}//${host}/`; a.textContent = host.replace(/:\d+$/, ''); } })();
