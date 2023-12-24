let x = 0,
  y = 0,
  dirX = 1,
  dirY = 1;
const speed = 1;
const pallete = ["#ff8800", "#e124ff", "#6a19ff", "#ff2188"];
let prevColorChoiceIndex = 0;

let animationId = null;
let animationRunning = false;
var fps = 30;

function getNewRandomColor() {
  const currentPallete = [...pallete];
  currentPallete.splice(prevColorChoiceIndex, 1);
  const colorChoiceIndex = Math.floor(Math.random() * currentPallete.length);
  prevColorChoiceIndex =
    colorChoiceIndex < prevColorChoiceIndex
      ? colorChoiceIndex
      : colorChoiceIndex + 1;
  const colorChoice = currentPallete[colorChoiceIndex];
  return colorChoice;
}

function animate() {
  setTimeout(() => {
    let dvd = document.getElementById("dvd");
    const screenHeight = document.body.clientHeight;
    const screenWidth = document.body.clientWidth;
    if (!dvd.style.backgroundColor) dvd.style.backgroundColor = pallete[0];
    const dvdWidth = dvd.clientWidth;
    const dvdHeight = dvd.clientHeight;

    if (y + dvdHeight >= screenHeight || y < 0) {
      dirY *= -1;
      dvd.style.backgroundColor = getNewRandomColor();
    }
    if (x + dvdWidth >= screenWidth || x < 0) {
      dirX *= -1;

      dvd.style.backgroundColor = getNewRandomColor();
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
