/**
 * Logintel API — JavaScript integration example (fetch / Node.js 18+).
 *
 * No dependencies required (uses built-in fetch).
 */

const BASE_URL = "http://localhost:8000"; // replace with your deployment URL
const API_KEY = "YOUR_API_KEY";

const headers = {
  "Content-Type": "application/json",
  "X-API-Key": API_KEY,
};

async function request(path, options = {}) {
  const resp = await fetch(`${BASE_URL}${path}`, { headers, ...options });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(`${options.method ?? "GET"} ${path} failed: ${data.error.message}`);
  }
  return data;
}

async function main() {
  // 1. Create a prediction (Milan -> Rome), departing tomorrow
  const departure = new Date(Date.now() + 24 * 60 * 60 * 1000);
  const prediction = await request("/v1/predictions", {
    method: "POST",
    body: JSON.stringify({
      origin: { lat: 45.4642, lon: 9.19 },
      destination: { lat: 41.9028, lon: 12.4964 },
      departure_time: departure.toISOString(),
      include_alternatives: true,
    }),
  });

  console.log(`Prediction created: ${prediction.id}`);
  console.log(`  Total delay: ${prediction.total_delay_minutes} min`);
  console.log(`  Confidence: ${prediction.confidence.overall}% (${prediction.confidence.level})`);
  console.log(`  Segments: ${prediction.segments.length}`);

  for (const alt of prediction.alternatives) {
    console.log(`  Alternative ${alt.route_index}: saves ${alt.delay_savings_minutes} min`);
  }

  // 2. Retrieve the prediction
  const retrieved = await request(`/v1/predictions/${prediction.id}`);
  console.log(`\nRetrieved prediction ${retrieved.id}: ${retrieved.total_delay_minutes} min delay`);

  // 3. Submit feedback (after the trip)
  const feedback = await request(`/v1/predictions/${prediction.id}/feedback`, {
    method: "POST",
    body: JSON.stringify({
      actual_delay_minutes: 35,
      notes: "Heavy rain near Florence",
    }),
  });
  console.log("\nFeedback submitted:");
  console.log(`  Predicted: ${feedback.predicted_delay_minutes} min`);
  console.log(`  Actual: ${feedback.actual_delay_minutes} min`);
  console.log(`  Deviation: ${feedback.deviation_minutes} min`);

  // 4. Check accuracy analytics
  const analytics = await request("/v1/analytics/accuracy");
  console.log("\nAccuracy analytics:");
  console.log(`  Total predictions: ${analytics.total_predictions}`);
  console.log(`  MAE: ${analytics.mae} min`);
  console.log(`  Within 10 min: ${analytics.within_10min_pct}%`);
  console.log(`  Calibration version: ${analytics.calibration_version}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
