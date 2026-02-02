new Chart(canvas.getContext("2d"), {
  type: "pie",
  data: {
    labels: Object.keys(data),
    datasets: [{
      data: Object.values(data),
      backgroundColor: ["#f59e0b", "#2563eb", "#16a34a"]
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false
  }
});
