import requests

url = "https://api-a-c7818b61-600.sptpub.com/api/v1/coupon/max"

payload = {
    "type": "1/1",
    "sum": "5",
    "k": "3.35",
    "global_id": None,
    "bonus_id": None,
    "bet_request_id": "2626372418041688097-1--2",
    "odds_change": "higher",
    "selections": [
        {
            "event_id": "2626372418041688097",
            "market_id": "1",
            "specifiers": "",
            "outcome_id": "2",
            "k": "3.35",
            "source": {
                "layout": "tile",
                "page": "/:sportSlugAndId",
                "section": "Promo",
                "extra": {
                    "market": "sport_page",
                    "timeFilter": "",
                    "banner_type": "auto_events",
                    "tab": ""
                }
            },
            "promo_id": None,
            "bonus_id": None,
            "timestamp": 1771505164011
        }
    ]
}
headers = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "authorization": "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzI1NiJ9.eyJpc3MiOiIyNDgyOTc1NjAxMTkxOTUyMzg2Iiwic3ViIjoiNTA1Mzg3NiIsIm5hbWUiOiJSYWluTWFuMDEiLCJpYXQiOjE3NzE1MTIwODQsImV4cCI6MTc3MTUxOTI4NCwianRpIjoiNjk5NzIxMTQ4ODAxMDpVU0Q6VVNEVDplbiIsImxhbmciOiJlbiIsImN1cnJlbmN5IjoiVVNEIn0.VUll24dUzWAl-5vNnZxunDxuvNp9O0-zki_Ewp-NTbLVHFDE8J3KqFsTPjPPeU5EFl0zcxFnVwKje8hm4M4iBA",
    "content-type": "application/json",
    "origin": "https://duel.com",
    "priority": "u=1, i",
    "referer": "https://duel.com/",
    # "sec-ch-ua": ""Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"",
    # "sec-ch-ua-mobile": "?1",
    # "sec-ch-ua-platform": '"Android"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "cross-site",
    "user-agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36"
}

response = requests.request("POST", url, json=payload, headers=headers)

print(response.text)