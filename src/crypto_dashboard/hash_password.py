import bcrypt
import sys

def hash_password(password):
    # bcrypt.gensalt() generates a random salt
    # bcrypt.hashpw() hashes the password with the generated salt
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    return hashed.decode('utf-8')

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python hash_password.py <your_password>")
        sys.exit(1)

    plain_password = sys.argv[1]
    hashed_pw = hash_password(plain_password)
    print(f"Hashed Password: {hashed_pw}")
    print("\nCopy this hashed password into your .env file for the LOGIN_PASSWORD variable.")
