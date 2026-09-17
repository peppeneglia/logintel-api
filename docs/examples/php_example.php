<?php
/**
 * Logintel API — PHP integration example (cURL).
 *
 * Requirements: PHP 8.0+ with the cURL extension.
 */

declare(strict_types=1);

const BASE_URL = "http://localhost:8000"; // replace with your deployment URL
const API_KEY  = "YOUR_API_KEY";

function apiRequest(string $method, string $path, ?array $body = null): array
{
    $ch = curl_init(BASE_URL . $path);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        "Content-Type: application/json",
        "X-API-Key: " . API_KEY,
    ]);

    if ($method === "POST") {
        curl_setopt($ch, CURLOPT_POST, true);
        if ($body !== null) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
        }
    }

    $response = curl_exec($ch);
    if ($response === false) {
        $error = curl_error($ch);
        curl_close($ch);
        throw new RuntimeException("Request failed: {$error}");
    }
    $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    $data = json_decode($response, true);

    if ($httpCode >= 400) {
        $message = $data["error"]["message"] ?? "Unknown error";
        throw new RuntimeException("HTTP {$httpCode}: {$message}");
    }

    return $data;
}

// 1. Create a prediction (Milan -> Rome), departing tomorrow
echo "=== Create Prediction ===\n";
$departure = (new DateTimeImmutable("+1 day", new DateTimeZone("UTC")))->format(DATE_ATOM);
$prediction = apiRequest("POST", "/v1/predictions", [
    "origin"               => ["lat" => 45.4642, "lon" => 9.1900],
    "destination"          => ["lat" => 41.9028, "lon" => 12.4964],
    "departure_time"       => $departure,
    "include_alternatives" => true,
]);

$predictionId = $prediction["id"];
echo "  ID: {$predictionId}\n";
echo "  Total delay: {$prediction['total_delay_minutes']} min\n";
echo "  Confidence: {$prediction['confidence']['overall']}% ({$prediction['confidence']['level']})\n";

foreach ($prediction["alternatives"] as $alt) {
    echo "  Alternative {$alt['route_index']}: saves {$alt['delay_savings_minutes']} min\n";
}

// 2. Retrieve the prediction
echo "\n=== Get Prediction ===\n";
$retrieved = apiRequest("GET", "/v1/predictions/{$predictionId}");
echo "  Retrieved: {$retrieved['total_delay_minutes']} min delay\n";

// 3. Submit feedback (after the trip)
echo "\n=== Submit Feedback ===\n";
$feedback = apiRequest("POST", "/v1/predictions/{$predictionId}/feedback", [
    "actual_delay_minutes" => 35,
    "notes"                => "Heavy rain near Florence",
]);
echo "  Predicted: {$feedback['predicted_delay_minutes']} min\n";
echo "  Actual: {$feedback['actual_delay_minutes']} min\n";
echo "  Deviation: {$feedback['deviation_minutes']} min\n";

// 4. Check accuracy analytics
echo "\n=== Accuracy Analytics ===\n";
$analytics = apiRequest("GET", "/v1/analytics/accuracy");
echo "  Total predictions: {$analytics['total_predictions']}\n";
echo "  MAE: {$analytics['mae']} min\n";
echo "  Within 10 min: {$analytics['within_10min_pct']}%\n";
echo "  Calibration version: {$analytics['calibration_version']}\n";
