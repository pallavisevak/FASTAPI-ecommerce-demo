from collections import OrderedDict
import re
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr, ValidationError, constr, validator
from pymongo import MongoClient
from bson import ObjectId
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
# from passlib.context import CryptContext
from pymongo.errors import PyMongoError
from secrets import token_urlsafe
from typing import Collection, Dict, List, Optional, Union, Any
from fastapi import FastAPI, HTTPException, status, Query, Path, Form

app = FastAPI()

app=FastAPI()
templates = Jinja2Templates(directory="templates")
client=MongoClient("mongodb+srv://system:root@cluster0.mpe5d9y.mongodb.net/?retryWrites=true&w=majority")
db=client["ecommerce"]
products_collection=db["products"]
users_collection=db["users"]
carts_collection=db["carts"]
orders_collection=db["orders"]

class User(BaseModel):
    username: constr(min_length=1,max_length=8)
    email: EmailStr
    password: str
    @validator("username")
    def validate_username(cls, value):
        if not value.isalpha():
            raise ValueError("Username must contain only alphabetic characters")
        return value

class Token(BaseModel):
    access_token: str
    token_type: str

class Product(BaseModel):
    name: str
    description: str
    price: int
    quantity: int

class ProductDetails(BaseModel):
    name: str
    description: str
    price: float

class ShoppingCartItem(BaseModel):
    quantity: int
    details: ProductDetails

class ShoppingCart(BaseModel):
    products: Dict[str, ShoppingCartItem]

class OrderItem(BaseModel):
    product_name: str
    quantity: int
    price: float

class Order(BaseModel):
    user_id: str
    total_amount: float
    items: List[OrderItem]

class Profile(BaseModel):
    username: str
    email: str

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@app.post("/register/", response_model=User)
async def register_user(user: User):
    user_exist = users_collection.find_one({"username": user.username})
    if user_exist:
        raise HTTPException(status_code=400, detail="Username already registered")
    if not user.username.isalpha() and len(user.username)>5:
        raise HTTPException(status_code=400, detail="Username must contain only alphabetic characters")

    email_pattern = re.compile(r'^[\w\.-]+@[a-zA-Z\d\.-]+\.[a-zA-Z]{2,}$')
    if not re.match(email_pattern, user.email):
        raise HTTPException(status_code=400, detail="Invalid email format: User@gmail.com")

    if len(user.password) <= 5:
        raise HTTPException(status_code=400, detail="Password must be at least 5 characters long")
    user_data = user.dict()
    user_data["hashed_password"] = user.password
    del user_data["password"]
    user_data["token"] = ""
    user_id = users_collection.insert_one(user_data).inserted_id

    return {"user_id": str(user_id), **user.dict()}


@app.post("/token", response_model=Token)
async def login_for_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = users_collection.find_one({"username": form_data.username, "hashed_password": form_data.password})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = token_urlsafe()
    users_collection.update_one({"_id": user["_id"]}, {"$set": {"token": token}})
    return {"access_token": token, "token_type": "bearer"}

async def get_current_user(token: str = Depends(oauth2_scheme)):
    user_data = users_collection.find_one({"token": token})
    if user_data:
        return user_data
    else:
        raise HTTPException(status_code=401, detail="Invalid credentials")

@app.get("/current_user", response_model=Dict[str, str])
async def read_current_user(current_user: dict = Depends(get_current_user)):
    return {"user_id": str(current_user["_id"]), "username": current_user["username"], "email": current_user["email"]}

@app.post("/products", response_model=dict)
def add_product(product: Product):
    try:
        product_data = dict(product.dict())
        product_id = str(products_collection.insert_one(product_data).inserted_id)
        return {"message": "Product added successfully", "product_id": product_id}
    except PyMongoError as e:
        raise HTTPException(status_code=500, detail=f"Error adding product: {str(e)}")

@app.get("/products/", response_model=List[Dict[str, Union[str, Dict[str, Any]]]])
async def display_data():
    products_data = products_collection.find({}, {"_id": 1, "name": 1, "description": 1, "price": 1, "quantity": 1})
    if not products_data:
        raise HTTPException(status_code=404, detail="No products found")
    
    products_list = [
        {
            "product_id": str(product["_id"]),
            "product_data": {
                "name": product["name"],
                "description": product["description"],
                "price": product["price"],
                "quantity": product["quantity"]
            }
        }
        for product in products_data
    ]
    return products_list

@app.get("/products/ids", response_model=List[str])
async def get_product_ids():
    product_ids = products_collection.distinct("_id")
    if not product_ids:
        raise HTTPException(status_code=404, detail="No product found")
    
    product_ids_str = [str(product_id) for product_id in product_ids]
    return product_ids_str

@app.post("/cart/add/{product_name}", status_code=200)
async def add_to_cart(product_name: str, quantity: int, current_user: User = Depends(get_current_user)):
    try:
        cart = current_user.get("cart", {"products": {}})
        product_details = products_collection.find_one({"name": product_name}, {"price": 1})
        if not product_details:
            raise HTTPException(status_code=404, detail=f"Product '{product_name}' not found")

        base_price = float(product_details["price"])

        if product_name in cart["products"]:
            existing_quantity = cart["products"][product_name]["quantity"]
            new_quantity = existing_quantity + quantity
            total_price = calculate_price(base_price, new_quantity)
            cart["products"][product_name]["quantity"] = new_quantity
            cart["products"][product_name]["details"]["price"] = float(total_price)
        else:
            total_price = calculate_price(base_price, quantity)
            cart["products"][product_name] = {
                "quantity": quantity,
                "details": {
                    "name": product_name,
                    "description": f"Description for {product_name}",
                    "price": float(total_price)
                }
            }
        
        users_collection.update_one({"_id": current_user["_id"]}, {"$set": {"cart": cart}})
        return {"message": f"Product '{product_name}' added to cart successfully", "cart": cart}
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def calculate_price(base_price: float, quantity: int):
    try:
        result = base_price * quantity
        return result
    except Exception as e:
        print(f"Calculation Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/cart/view/", response_model=ShoppingCart)
async def view_cart(current_user: User = Depends(get_current_user)):
    try:
        return ShoppingCart.parse_obj(current_user.get("cart", {"products": {}}))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
 
    
@app.delete("/cart/remove/{product_name}", status_code=200)
async def remove_from_cart(product_name: str, current_user: User = Depends(get_current_user)):
    try:
        cart = current_user.get("cart", {"products": {}})
        if product_name not in cart["products"]:
            raise HTTPException(status_code=404, detail=f"Product '{product_name}' not found in the cart")
        del cart["products"][product_name]

        users_collection.update_one({"_id": current_user["_id"]}, {"$set": {"cart": cart}})
        return {"message": f"Product '{product_name}' removed from the cart successfully", "cart": cart}
    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
# @app.post("/place-order/all", status_code=status.HTTP_201_CREATED)
# async def place_order_for_all(current_user: User = Depends(get_current_user)):
#     try:
#         cart = current_user.get("cart", {"products": {}})

#         # Calculate total amount, GST, and total amount with GST
#         total_amount = sum(item["details"]["price"] for item in cart["products"].values())
#         gst_rate = 0.18  # Assuming a GST rate of 18%
#         gst_amount = total_amount * gst_rate
#         total_amount_with_gst = total_amount + gst_amount

#         # Create an order document
#         order_data = {
#             "user_id": current_user["_id"],
#             "products": cart["products"],
#             "total_amount": total_amount,
#             "gst_amount": gst_amount,
#             "total_amount_with_gst": total_amount_with_gst,
#         }

#         # Save the order in the "orders" collection
#         order_id = orders_collection.insert_one(order_data).inserted_id

#         # Clear the user's cart after placing the order
#         users_collection.update_one({"_id": current_user["_id"]}, {"$set": {"cart": {"products": {}}}})

#         return {"order_id": str(order_id), "message": "Order placed successfully"}
#     except Exception as e:
#         print(f"Error: {e}")
#         raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@app.post("/place-order/all", response_model=Order)
async def place_order_for_all(current_user: User = Depends(get_current_user)):
    try:
        cart = current_user.get("cart", {"products": {}})
        if not cart["products"]:
            raise HTTPException(
                status_code=400,
                detail="Cart is empty."
            )
        order_data = {"user_id": str(current_user["_id"]), "total_amount": 0.0, "items": []}
        extra_charge = 18
        for product_name, product_details in cart["products"].items():
            product_details_db = products_collection.find_one({"name": product_name})
            if not product_details_db:
                raise HTTPException(
                    status_code=404,
                    detail=f"Product '{product_name}' not found in the database"
                )
            available_quantity = product_details_db.get("quantity", 0)
            ordered_quantity = product_details.get("quantity", 0)

            if ordered_quantity > available_quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient quantity for product '{product_name}'"
                )

            new_quantity = available_quantity - ordered_quantity
            products_collection.update_one(
                {"name": product_name},
                {"$set": {"quantity": new_quantity}}
            )

            order_data["total_amount"] += (ordered_quantity * product_details_db["price"])

            order_data["items"].append({
                "product_name": product_name,
                "quantity": ordered_quantity,
                "price": product_details_db["price"]
            })
        order_data["total_amount"] += extra_charge
        order_id = orders_collection.insert_one(order_data).inserted_id
        users_collection.update_one({"_id": current_user["_id"]}, {"$push": {"order_history": order_id}})
        users_collection.update_one({"_id": current_user["_id"]}, {"$set": {"cart": {"products": {}}}})
        order_data["_id"] = order_id
        return order_data
    except Exception as e:
        print(f"Error placing order for all products: {e}")
        raise HTTPException(status_code=500, detail="Failed to place order for all products")


@app.get("/products/search/", response_model=List[Product])
async def search_products(search: str=Query("",title="search query",description="serach product")):
    query={"$regex":f".*{search}.*","$options":"i"}
    product=list(products_collection.find({"$or":[{"name":query},{"description":query}]},{"_id":0}))
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

@app.get("/profile", response_model=Profile)
async def read_user_profile(current_user: User = Depends(get_current_user)):
    profile_data = Profile(username=current_user["username"], email=current_user["email"])
    return profile_data

@app.put("/profile/edit", response_model=Dict)
async def edit_profile(new_username: str, new_email: str, current_user: dict = Depends(get_current_user)
):
    try:
        result = users_collection.update_one(
            {"_id": ObjectId(current_user["_id"])},
            {"$set": {"username": new_username, "email": new_email}},
        )
        if result.matched_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        updated_profile = {
            "username": new_username,
            "email": new_email,
        }
        return updated_profile
    except Exception as e:
        print(f"Error updating profile: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to update profile",
        )
    
# @app.get("/profile/orders", response_model=List[Order])
# async def get_user_order_history(
#     current_user: User = Depends(get_current_user),
#     orders_collection: Collection = Depends(get_orders_collection),
# ):
#     try:
#         user_id = current_user["_id"]
#         print(f"Fetching orders for user ID: {user_id}")
#         user_orders = list(orders_collection.find({"user_id": user_id}))
#         print(f"User Orders: {user_orders}")
#         order_models = [Order(**order) for order in user_orders]
#         return order_models
#     except Exception as e:
#         print(f"Error fetching order history: {e}")
#         raise HTTPException(status_code=500, detail="Failed to fetch order history")



@app.get("/profile/orders", response_model=List[Order])
async def get_order_history(current_user: User = Depends(get_current_user)):
    try:
        user_id = str(current_user["_id"])
        order_history = orders_collection.find({"user_id": user_id})

        return list(order_history)
    except Exception as e:
        print(f"Error fetching order history: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch order history")

#client-side
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/registeryou", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("registeryou.html", {"request": request})

@app.get("/loginview", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("loginview.html", {"request": request})

@app.get("/addproduct", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("addproduct.html", {"request": request})

@app.get("/cartview", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("cartview.html", {"request": request})

@app.get("/products/search/",response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("searchproduct.html", {"request": request})

@app.get("/userprofile",response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("userprofile.html", {"request": request})