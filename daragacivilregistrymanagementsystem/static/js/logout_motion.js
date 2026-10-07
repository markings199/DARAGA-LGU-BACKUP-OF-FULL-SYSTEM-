(function () {
  var stage = document.getElementById("logout-stage");
  var link = document.querySelector('[data-logout="1"]');
  if (!stage || !link) return;

  var busy = false;
  var loginHref = stage.getAttribute("data-login-href") || "/login";
  var logoutHref = link.getAttribute("href") || "/logout";

  function reducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (e) {
      return false;
    }
  }

  function goLogin() {
    window.location.href = loginHref;
  }

  function endSession() {
    return fetch(logoutHref, {
      method: "GET",
      credentials: "same-origin",
      redirect: "follow",
      headers: { Accept: "text/html" }
    }).catch(function () {
      return null;
    });
  }

  function playThenLeave() {
    stage.removeAttribute("hidden");
    stage.setAttribute("aria-hidden", "false");
    window.requestAnimationFrame(function () {
      document.body.classList.add("is-logout");
    });
    window.setTimeout(function () {
      document.body.classList.add("is-logout-out");
    }, 720);
    window.setTimeout(goLogin, 1080);
  }

  function startLogout() {
    if (busy) return;
    busy = true;
    link.setAttribute("aria-busy", "true");
    endSession().then(function () {
      if (reducedMotion()) {
        goLogin();
        return;
      }
      playThenLeave();
    });
  }

  link.addEventListener("click", function (event) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    event.stopPropagation();
    startLogout();
  });
})();
