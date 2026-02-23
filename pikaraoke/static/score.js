function getScoreData(scoreValue) {
  function randomPhrase(phrases) {
    return phrases[Math.floor(Math.random() * phrases.length)];
  }

  if (scoreValue < 30) {
    return { applause: "applause-l.mp3", review: randomPhrase(scoreReviews.low) };
  } else if (scoreValue < 60) {
    return { applause: "applause-m.mp3", review: randomPhrase(scoreReviews.mid) };
  } else {
    return { applause: "applause-h.mp3", review: randomPhrase(scoreReviews.high) };
  }
}

function getScoreValue() {
  const random = Math.random();
  const bias = 2; // adjust this value to control the bias
  const scoreValue = Math.pow(random, 1 / bias) * 99;
  return Math.floor(scoreValue);
}

async function showFinalScoreWithAudio(
  scoreTextElement,
  scoreValue,
  scoreReviewElement,
  scoreData,
  applauseElement
) {
  scoreTextElement.text(String(scoreValue).padStart(2, "0"));
  scoreReviewElement.text(scoreData.review);
  launchFireworkShow(scoreValue);
  applauseElement.play();
  return new Promise((resolve) => {
    applauseElement.onended = resolve;
  });
}

async function rotateScore(scoreTextElement, duration) {
  const interval = 100;
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

async function startScore(staticPath) {
  try {
    const r = await fetch(PikaraokeConfig.scorePhrasesUrl);
    scoreReviews = await r.json();
  } catch (_e) {
    // Network failure: keep the last successfully fetched phrases
  }

  const scoreElement = $("#score");
  const scoreTextElement = $("#score-number-text");
  const scoreReviewElement = $("#score-review-text");

  const scoreValue = getScoreValue();
  const scoreData = getScoreData(scoreValue);

  const drums = new Audio(staticPath + "sounds/score-drums.mp3");
  // Pre-create applause audio NOW to capture the user activation window
  // Mobile Safari only allows audio.play() within a brief window after user events
  const applause = new Audio(staticPath + "sounds/" + scoreData.applause);

  scoreElement.show();
  drums.volume = 0.3;
  drums.play();
  const drumDuration = 4100;

  await rotateScore(scoreTextElement, drumDuration);
  await showFinalScoreWithAudio(
    scoreTextElement,
    scoreValue,
    scoreReviewElement,
    scoreData,
    applause
  );
  scoreReviewElement.text("");
  scoreElement.hide();
}
