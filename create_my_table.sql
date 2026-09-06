CREATE TABLE "my-table" (
    id SERIAL PRIMARY KEY,                 -- Otomatik artan birincil anahtar
    username VARCHAR(50) NOT NULL,         -- Boş olamaz, en fazla 50 karakter
    email VARCHAR(100) UNIQUE,             -- Tekil olmalı, en fazla 100 karakter
    age INTEGER DEFAULT 18,                -- Varsayılanı 18
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP -- Varsayılan şimdi
);
