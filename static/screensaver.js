$(function () {
  let x = 0,
    y = 0,
    dirX = 1,
    dirY = 1;
  const speed = 0.5;
  const pallete = ["#ff8800", "#e124ff", "#6a19ff", "#ff2188"];
  let dvd = document.getElementById("dvd");
  dvd.style.backgroundColor = pallete[0];
  let prevColorChoiceIndex = 0;
  let screensaver = document.getElementById("screensaver");
  const dvdWidth = dvd.clientWidth;
  const dvdHeight = dvd.clientHeight;

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
    const screenHeight = document.body.clientHeight;
    const screenWidth = document.body.clientWidth;

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
    window.requestAnimationFrame(animate);
  }

  window.requestAnimationFrame(animate);
});
