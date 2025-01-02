// Function that returns the applause sound and one of the reviews, based on the score value.
// The scoreReviews comes from the splash.html so that it can be translated.
function getScoreData(scoreValue) {
  if (scoreValue < 30) {
    return {
      applause: "applause-l.mp3",
      review:
        scoreReviews.low[Math.floor(Math.random() * scoreReviews.low.length)],
    };
  } else if (scoreValue < 60) {
    return {
      applause: "applause-m.mp3",
      review:
        scoreReviews.mid[Math.floor(Math.random() * scoreReviews.mid.length)],
    };
  } else {
    return {
      applause: "applause-h.mp3",
      review:
        scoreReviews.high[Math.floor(Math.random() * scoreReviews.high.length)],
    };
  }
}

// Function that creates a random score biased towards 99
function getScoreValue() {
  const random = Math.random();
  const bias = 2; // adjust this value to control the bias
  const scoreValue = Math.pow(random, 1 / bias) * 99;
  return Math.floor(scoreValue);
}

// Function that shows the final score value, the review, fireworks and plays the applause sound
async function showFinalScore(
  scoreTextElement,
  scoreValue,
  scoreReviewElement,
  scoreData
) {
  scoreTextElement.text(String(scoreValue).padStart(2, "0"));
  scoreReviewElement.text(scoreData.review);
  launchFireworkShow(scoreValue);
  const applauseElement = new Audio("static/sounds/" + scoreData.applause);
  applauseElement.play();
  return new Promise((resolve) => {
    applauseElement.onended = resolve;
  });
}

// Function that shows random numbers for the score suspense
async function rotateScore(scoreTextElement, duration) {
  interval = 100;
  const startTime = performance.now();

  while (true) {
    const elapsed = performance.now() - startTime;

    if (elapsed >= duration) break;

    const randomScore = String(Math.floor(Math.random() * 99) + 1).padStart(
      2,
      "0"
    );
    scoreTextElement.text(randomScore);

    const nextUpdate = interval - (performance.now() - (startTime + elapsed));
    await new Promise((resolve) =>
      setTimeout(resolve, Math.max(0, nextUpdate))
    );
  }
}

// Function that starts the score animation
async function startScore(staticPath) {
  const scoreElement = $("#score");
  const scoreTextElement = $("#score-number-text");
  const scoreReviewElement = $("#score-review-text");

  const scoreValue = getScoreValue();
  const drums = new Audio(staticPath + "sounds/score-drums.mp3");

  const scoreData = getScoreData(scoreValue);

  scoreElement.show();
  drums.volume = 0.3;
  drums.play();
  const drumDuration = 4100;

  await rotateScore(scoreTextElement, drumDuration);
  await showFinalScore(
    scoreTextElement,
    scoreValue,
    scoreReviewElement,
    scoreData
  );
  scoreReviewElement.text("");
  scoreElement.hide();
}
