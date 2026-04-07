import uncurl

# Paste your cURL command as a string
curl_cmd = """curl 'https://www.rentcafe.com/details/floorplans/modal-ds?propertyId=1780219&floorplanId=5194166&UnitId=undefined' --compressed -H 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:149.0) Gecko/20100101 Firefox/149.0' -H 'Accept: */*' -H 'Accept-Language: en-US,en;q=0.9' -H 'Accept-Encoding: gzip, deflate, br, zstd' -H 'Referer: https://www.rentcafe.com/apartments/or/portland/alta-art-tower-0/default.aspx' -H 'Content-Type: application/json' -H 'Connection: keep-alive' -H 'Sec-Fetch-Dest: empty' -H 'Sec-Fetch-Mode: cors' -H 'Sec-Fetch-Site: same-origin' -H 'Priority: u=0' -H 'TE: trailers'"""

# Convert to Python requests code
python_code = uncurl.parse(curl_cmd)
print(python_code)