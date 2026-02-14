"""
Logintel API — Python integration example (httpx async).

Install: pip install httpx
"""

import asyncio

import httpx

BASE_URL = "https://api.logintel.io"
API_KEY = "YOUR_API_KEY"
HEADERS = {
    "Content-Type": "application/json",
    "X-API-Key": API_KEY,
}


async def main():
    async with httpx.AsyncClient(base_url=BASE_URL, headers=HEADERS) as client:
        # 1. Create a prediction (Milan -> Rome)
        prediction_payload = {
            "origin": {"lat": 45.4642, "lon": 9.1900},
            "destination": {"lat": 41.9028, "lon": 12.4964},
            "departure_time": "2026-02-15T08:00:00+01:00",
            "include_alternatives": True,
        }

        resp = await client.post("/v1/predictions", json=prediction_payload)
        resp.raise_for_status()
        prediction = resp.json()

        prediction_id = prediction["id"]
        print(f"Prediction created: {prediction_id}")
        print(f"  Total delay: {prediction['total_delay_minutes']} min")
        print(f"  Confidence: {prediction['confidence']['overall']}% ({prediction['confidence']['level']})")
        print(f"  Segments: {len(prediction['segments'])}")

        if prediction["alternatives"]:
            for alt in prediction["alternatives"]:
                print(f"  Alternative {alt['route_index']}: saves {alt['delay_savings_minutes']} min")

        # 2. Retrieve the prediction
        resp = await client.get(f"/v1/predictions/{prediction_id}")
        resp.raise_for_status()
        retrieved = resp.json()
        print(f"\nRetrieved prediction {retrieved['id']}: {retrieved['total_delay_minutes']} min delay")

        # 3. Submit feedback (after the trip)
        feedback_payload = {
            "actual_delay_minutes": 35,
            "notes": "Heavy rain near Florence",
        }

        resp = await client.post(f"/v1/predictions/{prediction_id}/feedback", json=feedback_payload)
        resp.raise_for_status()
        feedback = resp.json()
        print(f"\nFeedback submitted:")
        print(f"  Predicted: {feedback['predicted_delay_minutes']} min")
        print(f"  Actual: {feedback['actual_delay_minutes']} min")
        print(f"  Deviation: {feedback['deviation_minutes']} min")

        # 4. Check accuracy analytics
        resp = await client.get("/v1/analytics/accuracy")
        resp.raise_for_status()
        analytics = resp.json()
        print(f"\nAccuracy analytics:")
        print(f"  Total predictions: {analytics['total_predictions']}")
        print(f"  MAE: {analytics['mae']} min")
        print(f"  Within 10 min: {analytics['within_10min_pct']}%")
        print(f"  Calibration version: {analytics['calibration_version']}")


if __name__ == "__main__":
    asyncio.run(main())
