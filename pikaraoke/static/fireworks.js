const canvas = document.getElementById("fireworks");
const ctx = canvas.getContext("2d");

// Ajusta o tamanho do canvas
canvas.width = window.innerWidth;
canvas.height = window.innerHeight;

// Função para gerar números aleatórios entre 1 e 100 com duas casas decimais
const getRandomNumber = () => String(Math.floor(Math.random() * 100) + 1).padStart(2, "0");

// Função para desenhar partículas de fogos
class Firework {
  constructor(x, y, color) {
    this.x = x;
    this.y = y;
    this.color = color;
    this.particles = Array.from({ length: 50 }, () => ({
      x: x,
      y: y,
      angle: Math.random() * 2 * Math.PI,
      speed: Math.random() * 2 + 1,
      radius: Math.random() * 6 + 3,
    }));
  }

  draw() {
    this.particles.forEach(particle => {
      const dx = Math.cos(particle.angle) * particle.speed;
      const dy = Math.sin(particle.angle) * particle.speed;
      particle.x += dx;
      particle.y += dy;
      particle.radius *= 0.98;

      ctx.beginPath();
      ctx.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
      ctx.fillStyle = this.color;
      ctx.fill();
    });
  }
}

// Configurações de fogos
let fireworks = [];
const addFirework = () => {
  const x = Math.random() * canvas.width;
  const y = Math.random() * canvas.height * 0.6;
  const color = `hsl(${Math.random() * 360}, 100%, 60%)`;
  fireworks.push(new Firework(x, y, color));
};

// Atualiza e renderiza os fogos
const animateFireworks = () => {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  fireworks.forEach((firework, index) => {
    firework.draw();
    firework.particles = firework.particles.filter(p => p.radius > 0.5);
    if (firework.particles.length === 0) fireworks.splice(index, 1);
  });

  if (fireworks.length > 0) {
    requestAnimationFrame(animateFireworks);
  }
};

const launchMultipleFireworks = (count) => {
	for (let i = 0; i < count; i++) {
	  addFirework();
	}
	animateFireworks();
 };

 const launchFireworkShow = (score) => {
	const showDuration = 5000; // Duração total do show em ms
	const startTime = Date.now();
	let simultaneousFireworks = 1;
	let intensity = 1300

	if (score < 30) {
	  simultaneousFireworks = 1;
	  intensity = 1300
	} else if (score < 60) {
	  simultaneousFireworks = 2;
	  intensity = 800
	} else if (score >= 60) {
	  simultaneousFireworks = 3;
	  intensity = 500
	}

	const launchInterval = () => {
	  if (Date.now() - startTime > showDuration) return; // Para após o tempo definido

	  const fireworkCount = Math.floor(Math.random() * simultaneousFireworks) + simultaneousFireworks; // Entre 2 e 5 fogos simultâneos
	  launchMultipleFireworks(fireworkCount);

	  const nextInterval = Math.random() * intensity + 200; // Intervalo entre 200ms e 1s
	  setTimeout(launchInterval, nextInterval); // Agenda o próximo grupo
	};

	launchInterval(); // Inicia o ciclo
 };
