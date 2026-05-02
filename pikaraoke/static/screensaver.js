let x = 0,
  y = 0,
  dirX = 1,
  dirY = 1;
const speed = 1;

let animationId = null;
let animationRunning = false;
var fps = 30;

function animate() {
  setTimeout(() => {
    let dvd = document.getElementById("dvd");
    const screenHeight = document.body.clientHeight;
    const screenWidth = document.body.clientWidth;
    const dvdWidth = dvd.clientWidth;
    const dvdHeight = dvd.clientHeight;

    if (y + dvdHeight >= screenHeight || y < 0) {
      dirY *= -1;
    }
    if (x + dvdWidth >= screenWidth || x < 0) {
      dirX *= -1;
    }
    x += dirX * speed;
    y += dirY * speed;
    dvd.style.left = x + "px";
    dvd.style.top = y + "px";
    animationRunning && window.requestAnimationFrame(animate);
  }, 1000 / fps);
}

function startScreensaver() {
  animationRunning = true;
  animationId = window.requestAnimationFrame(animate);
}

function stopScreensaver() {
  animationRunning = false;
  window.cancelAnimationFrame(animationId);
}
