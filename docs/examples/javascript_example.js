/**
 * Logintel API — JavaScript integration example (fetch / Node.js 18+).
 *
 * No dependencies required (uses built-in fetch).
 */

const BASE_URL = "https://api.logintel.io";
const API_KEY = "YOUR_API_KEY";

const headers = {
  "Content-Type": "application/json",
  "X-API-Key": API_KEY,
};

async function main() {
  // 1. Create a prediction (Milan -> Rome)
  const predictionPayload = {
    origin: { lat: 45.4642, lon: 9.19 },
    destination: { lat: 41.9028, lon: 12.4964 },
    departure_time: "2026-02-15T08:00:00+01:00",
    include_alternatives: true,
  };

  let resp = await fetch(`${BASE_URL}/v1/predictions`, {
    method: "POST",
    headers,
    body: JSON.stringify(predictionPayload),
  });

  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(`Create prediction failed: ${err.error.message}`);
  }

  const prediction = await resp.json();
  const predictionId = prediction.id;

  console.log(`Prediction created: ${predictionId}`);
  console.log(`  Total delay: ${prediction.total_delay_minutes} min`);
  console.log(
    `  Confidence: ${prediction.confidence.overall}% (${prediction.confidence.level})`
  );
  console.log(`  Segments: ${prediction.segments.length}`);

  if (prediction.alternatives.length > 0) {
    for (const alt of prediction.alternatives) {
      console.log(
        `  Alternative ${alt.route_index}: saves ${alt.delay_savings_minutes} min`
      );
    }
  }

  // 2. Retrieve the prediction
  resp = await fetch(`${BASE_URL}/v1/predictions/${predictionId}`, {
    headers,
  });

  if (!resp.ok) throw new Error("Get prediction failed");

  const retrieved = await resp.json();
  console.log(
    `\nRetrieved prediction ${retrieved.id}: ${retrieved.total_delay_minutes} min delay`
  );

  // 3. Submit feedback (after the trip)
  const feedbackPayload = {
    actual_delay_minutes: 35,
    notes: "Heavy rain near Florence",
  };

  resp = await fetch(`${BASE_URL}/v1/predictions/${predictionId}/feedback`, {
    method: "POST",
    headers,
    body: JSON.stringify(feedbackPayload),
  });

  if (!resp.ok) {
    const err = await resp.json();
    throw new Error(`Submit feedback failed: ${err.error.message}`);
  }

  const feedback = await resp.json();
  console.log("\nFeedback submitted:");
  console.log(`  Predicted: ${feedback.predicted_delay_minutes} min`);
  console.log(`  Actual: ${feedback.actual_delay_minutes} min`);
  console.log(`  Deviation: ${feedback.deviation_minutes} min`);

  // 4. Check accuracy analytics
  resp = await fetch(`${BASE_URL}/v1/analytics/accuracy`, { headers });

  if (!resp.ok) throw new Error("Get analytics failed");

  const analytics = await resp.json();
  console.log("\nAccuracy analytics:");
  console.log(`  Total predictions: ${analytics.total_predictions}`);
  console.log(`  MAE: ${analytics.mae} min`);
  console.log(`  Within 10 min: ${analytics.within_10min_pct}%`);
  console.log(`  Calibration version: ${analytics.calibration_version}`);
}

main().catch(console.error);
