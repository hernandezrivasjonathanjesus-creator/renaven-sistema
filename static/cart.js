// cart.js
console.log('Cart JS loaded');

// Función para agregar productos al carrito sin recargar la página
async function addToCart(productId, quantity = 1) {
    try {
        const response = await fetch('/add_to_cart', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                product_id: productId,
                quantity: quantity
            })
        });

        const data = await response.json();

        if (response.ok) {
            // Podrías usar una librería como SweetAlert2 o un simple alert
            alert(data.message || 'Producto agregado al carrito');
            // Opcional: actualizar un contador en el navbar
            location.reload(); // Recarga para ver los cambios en el carrito
        } else {
            alert('Error: ' + data.error);
        }
    } catch (error) {
        console.error('Error al agregar al carrito:', error);
    }
}

// Función para eliminar productos
async function removeFromCart(productId) {
    if (!confirm('¿Estás seguro de eliminar este producto?')) return;

    try {
        const response = await fetch(`/remove_from_cart/${productId}`, {
            method: 'DELETE'
        });

        if (response.ok) {
            location.reload();
        } else {
            alert('No se pudo eliminar el producto');
        }
    } catch (error) {
        console.error('Error:', error);
    }
}