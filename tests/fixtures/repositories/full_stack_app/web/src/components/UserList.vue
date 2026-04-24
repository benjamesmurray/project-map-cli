<template><div>Users</div></template>
<script setup>
import axios from 'axios';
const fetchUsers = async () => {
    const res = await axios.get('/api/v1/users');
    return res.data;
};
</script>
