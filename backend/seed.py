"""Reseed the store from data/: npm run seed."""

from backend import store

if __name__ == "__main__":
    store.reseed()
    print("Reseeded the claims queue from data/.")
