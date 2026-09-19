import json

data = json.load(open('datasets/foods 1.json', encoding='utf-8'))
foods = data['foods']

# Check Sauteed Spinach And Mushrooms
spinach = [f for f in foods if 'Sauteed Spinach And Mushrooms' in f['common_name']]
if spinach:
    print(f"Food: {spinach[0]['common_name']}")
    print(f"Food groups: {spinach[0].get('food_group')}")
else:
    print("Sauteed Spinach And Mushrooms not found")
