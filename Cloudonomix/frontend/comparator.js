function comparePrices() {
  // Mock pricing logic
  const aws = (Math.random() * 10 + 15).toFixed(2);
  const azure = (Math.random() * 10 + 14).toFixed(2);
  const gcp = (Math.random() * 10 + 16).toFixed(2);

  document.getElementById("awsPrice").innerText = "₹" + aws;
  document.getElementById("azurePrice").innerText = "₹" + azure;
  document.getElementById("gcpPrice").innerText = "₹" + gcp;

  const min = Math.min(aws, azure, gcp);
  let cheapest = min == aws ? "AWS" : min == azure ? "Azure" : "GCP";

  document.getElementById("cheapest").innerText =
    `Cheapest Option: ${cheapest}`;

  document.getElementById("result").style.display = "block";
}
